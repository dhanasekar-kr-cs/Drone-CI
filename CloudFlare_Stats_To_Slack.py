import requests
from datetime import datetime, date, timedelta
import time
import json
import re
import os

# Configuration
API_TOKEN = os.getenv("API_TOKEN")
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
SLACK_CHANNEL_ID = "C079Z48QE49"

# Color codes for thresholds
RED_COLOR = "#ff0000"      # For hit ratio < 90%
YELLOW_COLOR = "#F7DC6F"   # For hit ratio between 90-95%
GREEN_COLOR = "#229954"    # For hit ratio >= 95%

def get_yesterday_date():
    """Get yesterday's date in UTC"""
    today = datetime.utcnow().date()
    yesterday = today - timedelta(days=1)
    return yesterday

def get_account_id():
    """Get the account ID for the API token"""
    url = "https://api.cloudflare.com/client/v4/accounts"
    headers = {
        "Authorization": f"Bearer {API_TOKEN}",
        "Content-Type": "application/json"
    }
    
    response = requests.get(url, headers=headers)
    if response.status_code != 200:
        print(f"Error fetching account ID: {response.status_code}")
        return None
        
    data = response.json()
    if not data["success"] or not data["result"]:
        print("No accounts found")
        return None
        
    # Return the first account ID
    return data["result"][0]["id"]

def get_aggregated_metrics(date, account_id):
    """Fetch aggregated metrics across all zones for a given day using GraphQL"""
    url = "https://api.cloudflare.com/client/v4/graphql"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {API_TOKEN}"
    }
    
    query = """
    {
      viewer {
        accounts(filter: {accountTag: "%s"}) {
          httpRequests1dGroups(limit: 1, filter: {date: "%s"}) {
            dimensions {
              date
            }
            sum {
              requests
              bytes
              cachedBytes
              cachedRequests
              responseStatusMap {
                edgeResponseStatus
                requests
              }
            }
          }
        }
      }
    }
    """ % (account_id, date)
    
    payload = {"query": query}
    
    print(f"Sending GraphQL query with account ID: {account_id}")
    
    response = requests.post(url, headers=headers, json=payload)
    
    if response.status_code != 200:
        print(f"Error fetching metrics: {response.status_code}, {response.text}")
        return None
    
    response_data = response.json()
    print(f"GraphQL response (truncated): {str(response_data)[:500]}...")
    
    return response_data

def process_account_data(metrics_data):
    """Process account-based metrics data"""
    if not metrics_data or "data" not in metrics_data:
        print("No data available in response")
        return None
    
    data = metrics_data["data"]
    if not data or "viewer" not in data:
        print("No viewer data in response")
        return None
    
    viewer = data["viewer"]
    if not viewer or "accounts" not in viewer or not viewer["accounts"]:
        print("No accounts data in viewer or accounts is empty")
        return None
    
    accounts = viewer["accounts"]
    
    # Initialize aggregated metrics
    total_requests = 0
    total_cached_requests = 0
    total_bytes = 0
    total_cached_bytes = 0
    total_4xx = 0
    total_5xx = 0
    
    # Iterate through accounts and aggregate stats
    for account in accounts:
        if "httpRequests1dGroups" not in account or not account["httpRequests1dGroups"]:
            continue
        
        http_group = account["httpRequests1dGroups"][0]
        if "sum" not in http_group:
            continue
        
        stats = http_group["sum"]
        
        # Add to totals
        total_requests += stats.get("requests", 0)
        total_cached_requests += stats.get("cachedRequests", 0)
        total_bytes += stats.get("bytes", 0)
        total_cached_bytes += stats.get("cachedBytes", 0)
        
        # Process status codes
        if "responseStatusMap" in stats:
            for status in stats["responseStatusMap"]:
                code = status["edgeResponseStatus"]
                count = status["requests"]
                
                # Categorize status codes
                try:
                    code_int = int(code)
                    if 400 <= code_int < 500:
                        total_4xx += count
                    elif 500 <= code_int < 600:
                        total_5xx += count
                except ValueError:
                    pass  # Skip invalid status codes
    
    return {
        "total_requests": total_requests,
        "total_cached_requests": total_cached_requests,
        "total_bytes": total_bytes,
        "total_cached_bytes": total_cached_bytes,
        "total_4xx": total_4xx,
        "total_5xx": total_5xx
    }

def bytes_to_tib(bytes_value):
    """Convert bytes to TiB with 2 decimal places"""
    return f"{bytes_value / (1024**4):.2f} TiB"

def format_number(num):
    """Format number with appropriate suffix (K, M, B) matching example format"""
    if num < 1000:
        return f"{num}"
    elif num < 1000000:
        return f"{num/1000:.2f}K"
    elif num < 1000000000:
        return f"{num/1000000:.2f}M"
    else:
        return f"{num/1000000000:.2f}B"

def get_hit_ratio_color(hit_ratio):
    """Return the appropriate color code based on hit ratio thresholds"""
    if hit_ratio < 90:
        return RED_COLOR
    elif hit_ratio < 95:
        return YELLOW_COLOR
    else:
        return GREEN_COLOR

def send_to_slack(target_date, hit_ratio, cache_coverage, total_requests, origin_fetches, bandwidth, status_4xx, status_5xx):
    """Send message to Slack using bot token and channel ID with attachment for colored vertical line"""
    url = "https://slack.com/api/chat.postMessage"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {SLACK_BOT_TOKEN}"
    }
    
    # Get color based on hit ratio
    color = get_hit_ratio_color(hit_ratio)
    
    # Format the text with all stats
    text = (
        f"*Avg Hit Ratio: {hit_ratio:.2f}%*\n"
        f"*Avg Cache Coverage: {cache_coverage:.2f}%*\n"
        f"*CDN Requests: {total_requests}*\n"
        f"*CDN Origin Fetches: {origin_fetches}*\n"
        f"*CDN Bandwidth: {bandwidth}*\n"
        f"*CDN Status 4xx Requests: {status_4xx}*\n"
        f"*CDN Status 5xx Requests: {status_5xx}*"
    )
    
    # Create message with attachment that includes colored vertical line
    payload = {
        "channel": SLACK_CHANNEL_ID,
        "text": f"Cloudflare Stats *{target_date}*",
        "attachments": [
            {
                "fallback": "Cloudflare Performance Stats",
                "color": color,  # This creates the vertical colored line
                "text": text,
                "author_name": "Cloudfare Performance Stats"
            }
        ]
    }
    
    response = requests.post(url, headers=headers, json=payload)
    
    if response.status_code != 200:
        print(f"Error sending to Slack: {response.status_code}, {response.text}")
        return False
    
    slack_response = response.json()
    if not slack_response.get("ok", False):
        print(f"Slack API error: {slack_response.get('error', 'Unknown error')}")
        return False
        
    return True

def main():
    print("Starting Cloudflare metrics collection for previous day...")
    
    # Get yesterday's date
    yesterday = get_yesterday_date()
    formatted_yesterday = yesterday.strftime("%Y-%m-%d")
    
    # Get account ID
    account_id = get_account_id()
    if not account_id:
        print("Failed to retrieve account ID - cannot proceed")
        return
    
    # Process previous day's data
    print(f"Processing data for: {formatted_yesterday}")
    
    # Get metrics
    metrics_data = get_aggregated_metrics(formatted_yesterday, account_id)
    if not metrics_data:
        print(f"Failed to retrieve metrics for {formatted_yesterday}")
        return
        
    # Process metrics
    stats = process_account_data(metrics_data)
    if not stats:
        print(f"Failed to process metrics for {formatted_yesterday}")
        return
        
    # Extract metrics
    total_requests = stats["total_requests"]
    total_cached_requests = stats["total_cached_requests"]
    total_bytes = stats["total_bytes"]
    total_cached_bytes = stats["total_cached_bytes"]
    total_4xx = stats["total_4xx"]
    total_5xx = stats["total_5xx"]
    
    # Calculate derived metrics
    origin_fetches = total_requests - total_cached_requests
    hit_ratio = (total_cached_requests / total_requests * 100) if total_requests > 0 else 0
    cache_coverage = (total_cached_bytes / total_bytes * 100) if total_bytes > 0 else 0
    
    # Format for Slack display
    formatted_requests = format_number(total_requests)
    formatted_origin_fetches = format_number(origin_fetches)
    formatted_bandwidth = bytes_to_tib(total_bytes)
    formatted_4xx = format_number(total_4xx)
    formatted_5xx = format_number(total_5xx)
    
    # Print stats summary
    print(f"\n=== CloudFlare Stats Summary for {formatted_yesterday} ===")
    print(f"Hit Ratio: {hit_ratio:.2f}%")
    print(f"Cache Coverage: {cache_coverage:.2f}%")
    print(f"Total Requests: {formatted_requests}")
    print(f"Origin Fetches: {formatted_origin_fetches}")
    print(f"Total Bandwidth: {formatted_bandwidth}")
    print(f"4xx Errors: {formatted_4xx}")
    print(f"5xx Errors: {formatted_5xx}")
    print("=========================================\n")
    
    # Send to Slack
    print("Sending data to Slack...")
    slack_result = send_to_slack(
        formatted_yesterday,
        round(hit_ratio, 2),
        round(cache_coverage, 2),
        formatted_requests,
        formatted_origin_fetches,
        formatted_bandwidth,
        formatted_4xx,
        formatted_5xx
    )
    
    if slack_result:
        print("Successfully sent CloudFlare stats to Slack!")
        print("Script completed - no data saved locally (as requested)")
    else:
        print("Failed to send to Slack.")

if __name__ == "__main__":
    main()

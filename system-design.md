System Design: Status Monitor & Cost Visibility Dashboard


1. Executive Summary:
A self-hosted uptime monitoring and cost tracking dashboard for small businesses. Runs on AWS Free Tier ($0/month) and monitors API endpoints every 60 seconds while tracking cloud costs via CSV imports.

Business Value: Provides expensive Datadog/CloudHealth functionality for a cheaper option.



2. Requirements

 Functional Requirements

  1  Monitor 3-5 HTTP/HTTPS endpoints every 60 seconds 
  2  Record status (up/down), latency (ms), status code, timestamp 
  3  Display current status with green/red indicators 
  4  Show uptime percentage for last 24 hours 
  5  Display latency graph for last 24 hours
  6  Import AWS daily cost data from CSV 
  7  Show daily cost chart by service  
  8  Keep 30 days of historical data  

 Non-Functional Requirements

  1  Dashboard load time - <2 seconds 
  2  Maximum endpoints supported - 10 (design) 
  3  Monitor uptime - Best effort 
  4  Data retention - 30 days 
  5  Check interval accuracy - ±5 seconds 

 Security Requirements

  1  No password login for dashboard - Public status page 
  2  API key required for write operations - X-API-Key header 
  3  SSH key only (no passwords) - SSH config 
  4  SSH restricted to home IP - Security group 
  5  SQLite file permissions 600 - chmod 
  6  No secrets in code - .env file 
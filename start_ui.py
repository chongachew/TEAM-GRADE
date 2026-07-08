#!/usr/bin/env python3
"""
Simple HTTP server for TEAM-GRADE upload UI
Serves the upload.html file with CORS support
"""

import os
import sys
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

class CORSHTTPRequestHandler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.end_headers()

    def log_message(self, format, *args):
        """Custom logging"""
        if 'GET' in format or 'POST' in format:
            print(f"[{self.log_date_time_string()}] {format % args}")


def main():
    # Change to workspace directory
    workspace_dir = Path(__file__).parent
    os.chdir(workspace_dir)
    
    port = 8080
    
    print("\n" + "=" * 70)
    print("TEAM-GRADE UPLOAD UI - HTTP SERVER")
    print("=" * 70)
    print(f"\n📍 Serving from: {workspace_dir}")
    print(f"\n🚀 Access UI at:")
    print(f"   → http://localhost:{port}/upload.html")
    print(f"   → http://127.0.0.1:{port}/upload.html")
    print(f"\n📡 API Connection:")
    print(f"   → Backend: http://localhost:8000/api")
    print(f"   → Status: Check if API server is running")
    print(f"\n⌨️  Press Ctrl+C to stop the server")
    print("=" * 70 + "\n")
    
    handler = CORSHTTPRequestHandler
    httpd = HTTPServer(('0.0.0.0', port), handler)
    
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[X] Server stopped")
        sys.exit(0)


if __name__ == '__main__':
    main()

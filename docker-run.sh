#!/bin/bash

# Stop and remove existing container if it exists
docker stop tournament-tracker-app 2>/dev/null || true
docker rm tournament-tracker-app 2>/dev/null || true

# Build the Docker image
docker build -t tournament-tracker .

# Run the container
docker run -d \
  --name tournament-tracker-app \
  -p 8000:8000 \
  -e CHALLONGE_USERNAME="$CHALLONGE_USERNAME" \
  -e CHALLONGE_API_KEY="$CHALLONGE_API_KEY" \
  --restart unless-stopped \
  tournament-tracker

echo "Application is running on http://localhost:8000"
echo "Check logs with: docker logs tournament-tracker-app"
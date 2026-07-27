#!/bin/bash
docker run -d --name nodeodm --restart unless-stopped -p 3000:3000 opendronemap/nodeodm

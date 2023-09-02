#!/usr/bin/env bash

sudo apt update -y
sudo apt upgrade -y
sudo apt install nginx ufw git vim python3-venv -y

# install node 20 sigh
sudo apt-get install -y ca-certificates curl gnupg
sudo mkdir -p /etc/apt/keyrings
curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | sudo gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg
NODE_MAJOR=20
echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_$NODE_MAJOR.x nodistro main" | sudo tee /etc/apt/sources.list.d/nodesource.list
sudo apt-get update
sudo apt-get install nodejs -y

sudo ufw allow ssh
sudo ufw allow 'Nginx HTTP'
sudo ufw enabled

# install the app
git clone git@github.com:facundoolano/feedi.git
make venv deps secret-key

# setup the app as a service
# TODO

# setup nginx as the proxy
sudo tee -a /etc/nginx/sites-available/feedi > /dev/null <<EOF
server {
    listen 80;
    server_name _;

    location ^~ /static/  {
        include  /etc/nginx/mime.types;
        root /home/pi/feedi/feedi/;
    }

    location / {
        proxy_pass http://127.0.0.1:5000/;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Host $host;
        proxy_set_header X-Forwarded-Prefix /;
    }
}
EOF

sud rm /etc/nginx/sites-enabled/default
sudo ln -s /etc/nginx/sites-available/feedi /etc/nginx/sites-enabled/feedi
sudo systemctl start nginx

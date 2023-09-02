#!/usr/bin/env bash

sudo apt update -y
sudo apt upgrade -y
sudo apt install nginx ufw git vim python3-venv nodejs npm -y

sudo ufw allow ssh
sudo ufw allow 'Nginx HTTP'
sudo ufw enabled

git clone git@github.com:facundoolano/feedi.git
make venv deps

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

sudo ln -s /etc/nginx/sites-available/feedi /etc/nginx/sites-enabled/feedi
sudo systemctl start nginx

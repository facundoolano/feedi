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

# setup the firewall
sudo ufw allow ssh
sudo ufw allow 'Nginx HTTP'
sudo ufw enabled

# install the app
git clone git@github.com:facundoolano/feedi.git
cd feedi
make venv deps secret-key
mkdir instance

# setup the app as a service
sudo groupadd feedi
sudo useradd feedi -g feedi
sudo chown feedi instance/
sudo chown feedi instance/*

sudo tee -a /etc/systemd/system/gunicorn.service > /dev/null <<EOF
[Unit]
Description=gunicorn daemon
Requires=gunicorn.socket
After=network.target

[Service]
Type=notify
User=feedi
Group=feedi
RuntimeDirectory=gunicorn
WorkingDirectory=/home/pi/feedi
Environment="FEEDI_CONFIG=feedi/config/prod.py"
ExecStart=/home/pi/feedi/venv/bin/gunicorn -b 127.0.0.1:5000 -k gevent 'feedi.app:create_app()'
ExecReload=/bin/kill -s HUP $MAINPID
KillMode=mixed
TimeoutStopSec=5
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF

sudo tee -a /etc/systemd/system/gunicorn.socket > /dev/null <<EOF
[Unit]
Description=gunicorn socket

[Socket]
ListenStream=/run/gunicorn.sock
SocketUser=www-data

[Install]
WantedBy=sockets.target
EOF

sudo systemctl enable gunicorn
sudo systemctl start gunicorn

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
        proxy_pass http://unix:/run/gunicorn.sock;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Host $host;
        proxy_set_header X-Forwarded-Prefix /;
    }
}
EOF

sud rm /etc/nginx/sites-enabled/default
sudo ln -s /etc/nginx/sites-available/feedi /etc/nginx/sites-enabled/feedi

sudo systemctl enable nginx
sudo systemctl start nginx

#!/usr/bin/env bash
# Setup the server and the feedi app as a service.
# The app will be installed in the current user's home dir.
# It should be run as sudo and with permissions to pull from github.
# Tested on a raspberry Pi OS but I assume should work on any debian

apt update -y
apt upgrade -y
apt install nginx ufw git vim python3-venv -y

# install node 20 sigh
apt-get install -y ca-certificates curl gnupg
mkdir -p /etc/apt/keyrings
curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg
NODE_MAJOR=20
echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_$NODE_MAJOR.x nodistro main" | tee /etc/apt/sources.list.d/nodesource.list
apt-get update
apt-get install nodejs -y

# setup the firewall
ufw allow ssh
ufw allow 'Nginx HTTP'
ufw enabled

# install the app
cd $HOME
git clone git@github.com:facundoolano/feedi.git
cd feedi
make venv deps secret-key
mkdir instance

# setup the app as a service
groupadd feedi
useradd feedi -g feedi
chown feedi instance/
chown feedi instance/*

cat <<EOF > /etc/systemd/system/gunicorn.service
[Unit]
Description=gunicorn daemon
Requires=gunicorn.socket
After=network.target

[Service]
Type=notify
User=feedi
Group=feedi
RuntimeDirectory=gunicorn
WorkingDirectory=$HOME/feedi
Environment="FEEDI_CONFIG=feedi/config/prod.py"
ExecStart=$HOME/feedi/venv/bin/gunicorn -b 127.0.0.1:5000 -k gevent 'feedi.app:create_app()'
ExecReload=/bin/kill -s HUP \$MAINPID
KillMode=mixed
TimeoutStopSec=5
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF

cat <<EOF > /etc/systemd/system/gunicorn.socket
[Unit]
Description=gunicorn socket

[Socket]
ListenStream=/run/gunicorn.sock
SocketUser=www-data

[Install]
WantedBy=sockets.target
EOF

systemctl enable gunicorn
systemctl start gunicorn

# setup nginx as the proxy
cat <<EOF > /etc/nginx/sites-available/feedi
server {
    listen 80;
    server_name _;

    location ^~ /static/  {
        include  /etc/nginx/mime.types;
        root $HOME/feedi/feedi/;
    }

    location / {
        proxy_pass http://unix:/run/gunicorn.sock;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header X-Forwarded-Host \$host;
        proxy_set_header X-Forwarded-Prefix /;
    }
}
EOF

sud rm /etc/nginx/sites-enabled/default
ln -s /etc/nginx/sites-available/feedi /etc/nginx/sites-enabled/feedi

systemctl enable nginx
systemctl start nginx

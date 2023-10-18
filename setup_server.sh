#!/usr/bin/env bash
# Setup the server and the feedi app as a service.
# The app will be installed in the running user's home directory and the
# service will run with a new feedi user.
# Tested on a raspberry Pi OS but I assume should work on any debian
#
# ssh pi@feedi.local 'bash -s' < setup_server.sh

set -e

sudo apt update -y
sudo apt upgrade -y
sudo apt install build-essential gcc python3 python3-dev python3-pip python3-venv python-is-python3 nginx ufw git vim  -y

# install node 20 sigh
sudo apt-get install -y ca-certificates curl gnupg
mkdir -p /etc/apt/keyrings
curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | sudo gpg --yes --dearmor -o /etc/apt/keyrings/nodesource.gpg
NODE_MAJOR=20
echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_$NODE_MAJOR.x nodistro main" | sudo tee /etc/apt/sources.list.d/nodesource.list
sudo apt-get update
sudo apt-get install nodejs -y

# setup the firewall
sudo ufw allow ssh
sudo ufw allow 'Nginx HTTP'
sudo ufw --force enable

FEEDI_DIR=/home/feedi

# create a user to run the service
sudo groupadd feedi || true
sudo adduser --disabled-login --disabled-password feedi || true
cd $FEEDI_DIR

# FIXME these steps should be done by the feedi user
# install the app
git clone https://github.com/facundoolano/feedi.git
cd feedi
make deps secret-key
mkdir -p instance

# disable default auth
sed -i '/DEFAULT_AUTH_USER/s/^# //g' feedi/config/production.py

touch instance/feedi.db
sudo chown -R feedi .

# FIXME do we really need this
# let others write so we can overwrite with scp
sudo chmod 666 instance/feedi.db

# allow other users to read static files so nginx can serve them
sudo chmod o+r -R feedi/static/
DIR=$FEEDI_DIR/feedi/feedi/static
while [[ $DIR != / ]]; do chmod +rx "$DIR"; DIR=$(dirname "$DIR"); done;

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
WorkingDirectory=$FEEDI_DIR/feedi
ExecStart=$FEEDI_DIR/feedi/venv/bin/gunicorn
ExecReload=/bin/kill -s HUP \$MAINPID
KillMode=mixed
TimeoutStopSec=5
PrivateTmp=true
LimitNOFILE=10240

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
        root $FEEDI_DIR/feedi/feedi/;
    }

    location / {
        proxy_pass http://unix:/run/gunicorn.sock;
        include proxy_params;
    }
}
EOF

sudo rm -f /etc/nginx/sites-enabled/default
sudo ln -sf /etc/nginx/sites-available/feedi /etc/nginx/sites-enabled/feedi

sudo systemctl enable nginx
sudo systemctl restart nginx

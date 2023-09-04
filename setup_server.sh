#!/usr/bin/env bash
# Setup the server and the feedi app as a service.
# The app will be installed in the current directory.
# It should be run as sudo and with permissions to pull from github.
# Tested on a raspberry Pi OS but I assume should work on any debian
#
# scp -p setup_server.sh pi@feedi.local:.
# sudo ./setup_server.sh

set -e

sudo apt update -y
sudo apt upgrade -y
sudo apt install nginx ufw git vim python3-venv -y

# install node 20 sigh
sudo apt-get install -y ca-certificates curl gnupg
mkdir -p /etc/apt/keyrings
curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --yes --dearmor -o /etc/apt/keyrings/nodesource.gpg
NODE_MAJOR=20
echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_$NODE_MAJOR.x nodistro main" | tee /etc/apt/sources.list.d/nodesource.list
apt-get update
apt-get install nodejs -y

# setup the firewall
ufw allow ssh
ufw allow 'Nginx HTTP'
ufw --force enable

# install the app
FEEDI_DIR=$(pwd)
git clone https://github.com/facundoolano/feedi.git
cd feedi
make venv deps secret-key
mkdir -p instance

# setup the app as a service
groupadd feedi || true
useradd feedi -g feedi || true
touch instance/feedi.db
chown -R feedi .
# let others write so we can overwrite with scp
chmod 666 instance/feedi.db

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
WorkingDirectory=$FEEDI_DIR/feedi
ExecStart=$FEEDI_DIR/feedi/venv/bin/gunicorn
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
        root $FEEDI_DIR/feedi/feedi/;
    }

    location / {
        proxy_pass http://unix:/run/gunicorn.sock;
        include proxy_params;
    }
}
EOF

rm -f /etc/nginx/sites-enabled/default
ln -sf /etc/nginx/sites-available/feedi /etc/nginx/sites-enabled/feedi

systemctl enable nginx
systemctl restart nginx

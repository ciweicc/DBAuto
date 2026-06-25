#!/bin/bash
set -e

mkdir -p "$DATA_DIR"

exec python app_modules/main.py
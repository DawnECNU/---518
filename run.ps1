$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not $env:MONGO_URI) {
    $env:MONGO_URI = "mongodb://127.0.0.1:27017"
}
if (-not $env:MONGO_DB_NAME) {
    $env:MONGO_DB_NAME = "survey_phase1"
}

python app.py

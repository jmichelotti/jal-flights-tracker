@echo off
cd /d "%~dp0"
set ANTHROPIC_API_KEY=
claude -p "Run a JAL flight tracking session as described in CLAUDE.md" --model claude-sonnet-4-6 --allowedTools "mcp__playwright__*,Bash,Read,Write" >> tracker-log.txt 2>&1

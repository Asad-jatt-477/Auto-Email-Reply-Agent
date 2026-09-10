@echo off
REM Phase 13 - Option A (Windows local deployment)
REM Task Scheduler mein is file ko "Action" ke tor pe point karo taake
REM agent startup pe ya har X minute pe khud chal jaye.
REM
REM Task Scheduler setup:
REM 1. Task Scheduler kholo -> Create Basic Task
REM 2. Trigger: "At startup" ya "Daily, repeat every X minutes"
REM 3. Action: "Start a program" -> is run_agent.bat file ko select karo
REM 4. Finish

cd /d %~dp0
call venv\Scripts\activate.bat
python main.py >> logs\task_scheduler_output.log 2>&1

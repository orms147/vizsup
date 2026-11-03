# vizsup — `make run` mở app desktop bằng venv của dự án (giống run-desktop.ps1).
# Cần GNU make (choco install make) và backend/.venv — xem README.md, mục Quick start.
# Mỗi dòng lệnh trong recipe PHẢI bắt đầu bằng tab.

PY := backend/.venv/Scripts/python.exe

.PHONY: run app
run:
ifeq ($(wildcard $(PY)),)
	@echo "venv not found at $(PY) - see README.md, Quick start"
	@exit 1
else
	$(PY) -m desktop.main
endif

# vizsup.exe + Desktop / Start menu shortcuts (C# compiler that ships with Windows)
app:
	powershell -NoProfile -ExecutionPolicy Bypass -File build-app.ps1

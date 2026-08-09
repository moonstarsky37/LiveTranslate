"""Thin launcher shim so `start.bat` and `.venv\\Scripts\\python.exe main.py`
keep working after the package move. Real code lives in sublume/."""

from sublume.app import main

if __name__ == "__main__":
    import multiprocessing

    multiprocessing.freeze_support()
    main()

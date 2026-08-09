"""Entry point for `python -m sublume`."""

from sublume.app import main

if __name__ == "__main__":
    import multiprocessing

    multiprocessing.freeze_support()
    main()

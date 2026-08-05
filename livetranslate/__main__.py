"""Entry point for `python -m livetranslate`."""

from livetranslate.main import main

if __name__ == "__main__":
    import multiprocessing

    multiprocessing.freeze_support()
    main()

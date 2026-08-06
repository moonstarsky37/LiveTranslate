"""Entry point for `python -m livetranslate`."""

from livetranslate.app import main

if __name__ == "__main__":
    import multiprocessing

    multiprocessing.freeze_support()
    main()

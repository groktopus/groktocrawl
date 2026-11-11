"""Optional Camelot enrichment isolated in a killable, page-bounded process."""

import json
import sys


def main():
    import camelot
    from tabulate import tabulate

    tables = camelot.read_pdf(sys.argv[1], pages=sys.argv[2], flavor="lattice")
    sys.stdout.write(
        json.dumps([tabulate(t.df, headers="keys", tablefmt="github") for t in tables])
    )


if __name__ == "__main__":
    main()

"""cf subcommands: one module per command.

Each module exposes::

    def register(subparsers) -> None   # add_parser(...) and its arguments
    def run(args) -> int | None        # do the work; raise CfError for user errors

cf.py discovers the modules with pkgutil, so adding a command is adding a file.
Globals (``--campaign``, ``-v``, ``-q``) are parsed by cf.py and set on
``args``; load the campaign with ``cflib.campaign.from_args(args)``.
"""

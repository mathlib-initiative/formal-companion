"""Stage 5 (run the fleet): bridges, transfer planning, remote commands, status.

The heavy runs execute on a server through `tend` + `agentbox`; this package
holds the agent bridges that tend shells out to, the planner that decides what
is rsynced there, and the status reader.  Nothing in here talks to a network
unless the caller passes ``--execute``.
"""

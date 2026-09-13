"""One module per exposed tool, each named after the tool it answers.

`stdio_server.py` imports the module whose name matches the tool a call names
and calls the function of that same name, so a handler is reached by naming it
and by nothing else: the package holds no registry to fall out of step with the
inventory the server declares.
"""

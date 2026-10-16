"""Parse every HTML template with Jinja2 so a typo is caught before anything is deployed."""
import os
import sys

from jinja2 import Environment, FileSystemLoader

env = Environment(loader=FileSystemLoader("templates"))
failed = False
for name in sorted(os.listdir("templates")):
    try:
        env.get_template(name)
        print("ok  ", name)
    except Exception as exc:  # TemplateSyntaxError and friends
        failed = True
        print("FAIL", name, "->", exc)
sys.exit(1 if failed else 0)

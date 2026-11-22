import os
import sys

# Inside your command handler's except ModuleNotFoundError as err:
here = os.path.dirname(__file__)
cmd_dir = os.path.join(here, "cmd")

here_items = os.listdir(here) if os.path.exists(here) else ["<missing>"]
cmd_items = os.listdir(cmd_dir) if os.path.exists(cmd_dir) else ["<no cmd dir>"]

debug_text = (
    f"Error: {err}\n"
    f"here={here}\n"
    f"here_items={here_items}\n"
    f"cmd_items={cmd_items}\n"
    f"sys.path={sys.path}"
)

return Response.json({"type": 4, "data": {"content": debug_text[:1900]}})

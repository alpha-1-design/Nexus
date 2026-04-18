"""Sample Nexus plugin — demonstrates all available hooks."""

from nexus.plugins import PluginBase, PluginHook


class SamplePlugin(PluginBase):
    name = "sample-plugin"
    version = "1.0.0"
    description = "Sample Nexus plugin with dangerous command warnings"
    hooks = [PluginHook.ON_TOOL_CALL, PluginHook.ON_RESPONSE]

    def on_tool_call(self, tool_name: str, args: dict, ctx: dict):
        if tool_name == "Bash":
            cmd = args.get("command", "")
            dangerous = ["rm -rf /", "sudo rm -rf", "dd if=", "> /dev/sd", "mkfs"]
            for pattern in dangerous:
                if pattern in cmd:
                    print(f"\033[93m⚠️  [sample-plugin] WARNING: Potentially dangerous command: {cmd[:60]}\033[0m")
        return args

    def on_response(self, content: str, ctx: dict) -> str:
        return content

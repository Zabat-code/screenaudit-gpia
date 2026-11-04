"""Informe #5: which apps get recorded.

Three modes, all data-driven (informe: "definir las aplicaciones"):
  all     - record everything (default)
  include - record only the listed exe/title fragments
  exclude - record everything except the listed ones

Matching is case-insensitive substring, so `chrome` matches chrome.exe,
GOOGLE-CHROME.exe and "chrome". No hardcoded app names live in this file.
"""


class Policy:
    def __init__(self, mode="all", include=None, exclude=None):
        if mode not in ("all", "include", "exclude"):
            raise ValueError(f"policy.mode invalido: {mode}")
        self.mode = mode
        self.include = [str(x).lower() for x in (include or [])]
        self.exclude = [str(x).lower() for x in (exclude or [])]

    @classmethod
    def from_config(cls, cfg):
        p = (cfg or {}).get("policy", {})
        return cls(p.get("mode", "all"), p.get("include"), p.get("exclude"))

    @staticmethod
    def _match(needle, hay):
        return needle in hay if hay else False

    def allows(self, exe, title):
        if self.mode == "all":
            return True
        e = (exe or "").lower()
        t = (title or "").lower()
        hit_i = any(self._match(n, e) or self._match(n, t) for n in self.include)
        hit_x = any(self._match(n, e) or self._match(n, t) for n in self.exclude)
        if self.mode == "include":
            return hit_i and not hit_x
        return not hit_x

    def reason(self, exe, title):
        if self.allows(exe, title):
            return "ok"
        return "filtered_by_policy"

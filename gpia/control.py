"""Informe #17: pausar y detener desde afuera, con permiso de administrador.

Un recorder que solo se para con Ctrl-C no sirve en produccion: alguien
tiene que poder cortar la grabacion sin estar sentado en la terminal del
usuario, y no al reves.

Canal por archivos, no socket. Tres razones:

  - funciona igual en Linux y en Windows sin dependencias,
  - sobrevive a que el recorder se caiga (el comando queda en disco),
  - es auditable: el comando es un archivo con quien lo mando y cuando.

Permiso: el comando se acepta solo si el actor puede leer el archivo de
token, que el administrador crea en una ruta del sistema. En POSIX se
ademas exige que el token sea de root. Un usuario sin privilegios no puede
escribir ahi, asi que no puede fabricar un comando. Si el token no existe,
el control se niega: por defecto cerrado.
"""
import json
import os
import sys
import time

PAUSE = "pause"
RESUME = "resume"
STOP = "stop"
COMMANDS = (PAUSE, RESUME, STOP)

CMD_FILE = "cmd.json"
APPLIED_FILE = "applied.json"


def token_path():
    """Ruta del token. Del sistema, no del perfil del usuario.

    `GPIA_CONTROL_TOKEN` cambia la ruta (util para pruebas y para máquinas
    donde `/etc` no existe), pero NO salta el chequeo de propietario: un
    token que no sea de root sigue sin valer. Cambiar la ruta no es
    escalonar privilegios.
    """
    env = os.environ.get("GPIA_CONTROL_TOKEN")
    if env:
        return env
    if os.name == "nt":
        base = os.environ.get("ProgramData", r"C:\ProgramData")
        return os.path.join(base, "ScreenAudit", "control.token")
    return "/etc/gpia/control.token"


def read_token():
    try:
        with open(token_path(), encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return None


def is_admin():
    """True si este proceso puede emitir comandos de control.

    Cierra por defecto: sin token no hay control. En POSIX el token tiene
    que ser de root ademas de legible, para que un admins que se lo pase a
    un usuario no habilite el control sin querer.
    """
    tok = read_token()
    if not tok:
        return False
    if os.name != "nt":
        try:
            st = os.stat(token_path())
        except OSError:
            return False
        if st.st_uid != 0:
            return False
    return True


def admin_control_root():
    """Carpeta de control que solo un administrador puede escribir.

    Necesaria en Windows, donde no se puede saber quien creo un archivo.
    alli la garantia la da la ACL de ProgramData, no el codigo.
    """
    if os.name == "nt":
        base = os.environ.get("ProgramData", r"C:\ProgramData")
        return os.path.join(base, "ScreenAudit", "control")
    return None


def require_admin(action):
    if is_admin():
        return True
    raise PermissionError(
        f"'{action}' requiere permisos de administrador.\n"
        f"  falta o no es valido el token: {token_path()}\n"
        f"  para crearlo (una vez, como root):\n"
        f"    sudo install -m 600 -d {os.path.dirname(token_path())}\n"
        f"    sudo sh -c 'openssl rand -hex 24 > {token_path()}'")


class PermissionDenied(Exception):
    pass


class ControlChannel:
    """Lado del recorder: mira la carpeta y aplica lo que haya.

    En Windows la carpeta de control va a ProgramData (solo admins escriben).
    En POSIX se queda junto a la sesion, y lo que protege es que el comando
    tenga que ser de root.
    """

    def __init__(self, session_dir, diag=None, session_key=None):
        root = admin_control_root()
        if root and session_key:
            self.dir = os.path.join(root, session_key)
        else:
            self.dir = os.path.join(session_dir, "control")
        self.diag = diag
        self._last_seq = -1
        self.admin_pause = False
        self.admin_requested = None
        self.applied = []

    def ensure(self):
        try:
            os.makedirs(self.dir, exist_ok=True)
        except OSError as exc:
            if self.diag:
                self.diag.error("control_dir", exc, dir=self.dir)
            return False
        return True

    def poll(self):
        """Devuelve un comando nuevo, o None. Consume lo que devuelve.

        La autorizacion se verifica AQUI, no en `issue()`. `issue()` es solo
        una comodidad del CLI: el archivo es legible y escribible por el
        usuario monitored, asi que si el recorder se fiara de el, cualquiera
        podria frenar la grabacion escribiendo el JSON a mano. Por eso se
        exige que el comando lo haya escrito root (POSIX) o que viva en la
        carpeta de control de ProgramData, que solo los administradores
        pueden escribir (Windows).

        Se ignora cualquier archivo mal formado en vez de tirar la sesion:
        un control corrupto no puede voltear la grabacion.
        """
        p = os.path.join(self.dir, CMD_FILE)
        try:
            with open(p, encoding="utf-8") as fh:
                doc = json.load(fh)
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            if self.diag:
                self.diag.error("control_read", exc, path=p)
            return None
        seq = doc.get("seq", 0)
        cmd = doc.get("cmd")
        if not isinstance(seq, int) or seq <= self._last_seq:
            return None                      # repetido o atrasado: ignorar
        self._last_seq = seq
        if cmd not in COMMANDS or not self._authorized(p, doc):
            if self.diag:
                self.diag.emit("control_rejected", cmd=str(cmd)[:40],
                               actor=str(doc.get("actor"))[:40],
                               reason=("comando desconocido" if cmd not in COMMANDS
                                       else "autorizado insufficientemente"))
            return None
        self.applied.append(doc)
        self.admin_requested = cmd
        if self.diag:
            self.diag.emit("control_applied", cmd=cmd,
                           actor=str(doc.get("actor"))[:60],
                           at=doc.get("at"), seq=seq)
        self._mark_applied(doc)
        return cmd

    def _authorized(self, path, doc):
        """¿Puede este actor frenar la grabacion?"""
        if os.name == "nt":
            # En Windows no se puede saber quien escribio un archivo. La
            # proteccion es que la carpeta viva en ProgramData, que por
            # defecto solo la puede escribir el grupo Administradores.
            return self._under_admin_root(path)
        try:
            st = os.stat(path)
        except OSError:
            return False
        if st.st_uid != 0:
            if self.diag:
                self.diag.emit("control_rejected",
                               reason=f"el archivo lo escribio uid={st.st_uid}, no root",
                               actor=str(doc.get("actor"))[:40])
            return False
        return True

    def _under_admin_root(self, path):
        root = admin_control_root()
        if not root:
            return False
        try:
            return os.path.abspath(path).startswith(os.path.abspath(root) + os.sep)
        except (OSError, ValueError):
            return False

    def _mark_applied(self, doc):
        try:
            with open(os.path.join(self.dir, APPLIED_FILE), "w",
                      encoding="utf-8") as fh:
                json.dump({"seq": doc.get("seq"), "cmd": doc.get("cmd"),
                           "actor": doc.get("actor"), "at": doc.get("at"),
                           "applied": time.strftime("%Y-%m-%dT%H:%M:%S")},
                          fh, ensure_ascii=False, indent=2)
        except OSError:
            pass

    def status(self):
        return {"admin_pause": self.admin_pause,
                "last_seq": self._last_seq,
                "requested": self.admin_requested,
                "applied": self.applied[-5:]}


def issue(session_dir, cmd, actor=None, dry_run=False, session_key=None):
    """Lado del administrador: escribe un comando. Devuelve el doc emitido."""
    if cmd not in COMMANDS:
        raise ValueError(f"comando desconocido: {cmd} (use: {', '.join(COMMANDS)})")
    require_admin(cmd)
    actor = actor or _default_actor()
    root = admin_control_root()
    if root and session_key:
        cdir = os.path.join(root, session_key)
    else:
        cdir = os.path.join(session_dir, "control")
    if dry_run:
        return {"cmd": cmd, "actor": actor, "dir": cdir, "dry_run": True}
    os.makedirs(cdir, exist_ok=True)
    seq = _next_seq(cdir)
    doc = {"seq": seq, "cmd": cmd, "actor": actor,
           "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "pid": os.getpid()}
    tmp = os.path.join(cdir, ".cmd.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, os.path.join(cdir, CMD_FILE))   # atomico
    return doc


def _next_seq(cdir):
    try:
        with open(os.path.join(cdir, APPLIED_FILE), encoding="utf-8") as fh:
            return int(json.load(fh).get("seq", 0)) + 1
    except (OSError, ValueError, TypeError):
        return 1


def _default_actor():
    try:
        import getpass
        return f"{getpass.getuser()}@{os.uname().nodename}" if os.name != "nt" \
            else f"{getpass.getuser()}@{os.environ.get('COMPUTERNAME', '?')}"
    except Exception:
        return f"pid:{os.getpid()}"


def latest_session(root, service=None, employee=None):
    """Sesion abierta mas reciente, para mandar control sin buscarla a mano."""
    base = os.path.join(root, _slug(service)) if service else root
    best = None
    for dirpath, dirnames, _files in os.walk(base):
        if "state.json" in os.listdir(dirpath) or "gp_log.jsonl" in os.listdir(dirpath):
            if service and _slug(service) not in dirpath.replace(os.sep, "/"):
                continue
            try:
                m = os.path.getmtime(os.path.join(dirpath, "gp_log.jsonl"))
            except OSError:
                continue
            if employee:
                try:
                    with open(os.path.join(dirpath, "state.json"),
                              encoding="utf-8") as fh:
                        if json.load(fh).get("employee") != employee:
                            continue
                except (OSError, ValueError):
                    continue
            if best is None or m > best[0]:
                best = (m, dirpath)
    return best[1] if best else None


def _slug(s):
    from .paths import slugify
    return slugify(s) if s else "general"

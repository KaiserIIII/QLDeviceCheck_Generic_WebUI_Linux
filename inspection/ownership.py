"""OS-held database ownership; a crashed process releases its lease automatically."""
import os
from pathlib import Path


class DatabaseOwner:
    def __init__(self, db_path):
        path = Path(db_path).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        self.file = open(str(path) + '.owner', 'a+b')
        try:
            if os.name == 'nt':
                import msvcrt
                if self.file.seek(0, 2) == 0:
                    self.file.write(b'\0')
                    self.file.flush()
                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise RuntimeError('Database is already owned by another service') from None

    def close(self):
        if self.file.closed:
            return
        if os.name == 'nt':
            import msvcrt
            self.file.seek(0)
            msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)
        self.file.close()

import os
from app.models.task import Task
from decimal import Decimal

class Disk:

    MB_TO_BYTES = Decimal(1024 * 1024)

    @classmethod
    def directory_size(cls, path:str) -> Decimal:
        total = Decimal('0')

        def _scan(dir_path: str) -> Decimal:
            subtotal = Decimal('0')
            try:
                with os.scandir(dir_path) as entries:
                    for entry in entries:
                        try:
                            # follow_symlinks=False prevents infinite loops
                            if entry.is_file(follow_symlinks=False):
                                # st_size comes directly from the DirEntry stat buffer!
                                subtotal += Decimal(entry.stat(follow_symlinks=False).st_size)
                            elif entry.is_dir(follow_symlinks=False):
                                subtotal += _scan(entry.path)
                        except OSError:
                            pass
            except OSError:
                pass
            return subtotal

        return _scan(path)

    @classmethod
    def task_usage(cls, tasks) -> Decimal:
        """
        Calculates total disk usage for a single Task, a list of Tasks.
        """
        total_disk = Decimal('0')

        # Bound check: tasks empty
        if tasks is None:
            return total_disk

        # Normalize single Task instance into a iterable list
        if isinstance(tasks, Task):
            tasks = [tasks]
       
        # Sum directory sizes
        for t in tasks:
            # Check the size
            if t.size and t.size > 0.0:
                # USE THE TASK VALUE
                bytes_from_mb = Decimal(str(t.size)) * cls.MB_TO_BYTES
                total_disk += bytes_from_mb
            else:
                # CALCULATE
                path = t.task_path()
                if path and os.path.exists(path):
                    total_disk += cls.directory_size(path)

        return total_disk

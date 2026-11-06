"""Fork: an ambiguity checker for text-to-SQL agents.

    import fork
    report = fork.check("How many nonperforming loans are there?", agent_sql, "loans.sqlite")
    print(fork.to_text(report))
"""

from .check import check
from .db import Database
from .models import Dependency, Fork, Reading, Report
from .report import to_dict, to_json, to_markdown, to_text

__all__ = ["Database", "Dependency", "Fork", "Reading", "Report", "check", "to_dict", "to_json", "to_markdown", "to_text"]
__version__ = "0.0.1"

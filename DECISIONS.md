# DECISIONS.md

1. Indexes. I use a unique index on `employees.emp_code` and a unique compound index on `attendance_logs.emp_code, date`. The latter both prevents duplicate attendance records during concurrent punch-ins and supports employee/date lookups. I also index department/join date, attendance date/code, employee/date/status, and status/date for listing and reporting. I would verify every important plan with `explain` before adding more indexes, because every index increases write cost.

2. Punch-in race. Both requests may check the employee, but neither relies on a prior attendance lookup as the final guard. They attempt an insert. The unique compound index allows one insert and rejects the other; the duplicate-key error becomes HTTP 409.

3. Ties. The leaderboard uses MongoDB `$rank`, so equal totals share a rank and the next rank is skipped. Filtering is by `rank <= limit`, meaning tied employees at the cutoff are all returned, ordered by total minutes and then employee code.

4. Headcount. The department summary starts from employees and looks up their attendance logs. Employees with no logs remain in the result and count toward headcount if they joined by the end of the month.

5. 100x data. I would benchmark real query plans, add suitable compound indexes based on measured workloads, and consider pre-aggregated daily summaries for frequently requested analytics.

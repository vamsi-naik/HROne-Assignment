# REVIEW.md

| # | Where | What is wrong | How to notice | Fix |
|---|---|---|---|---|
| 1 | `compute_late_minutes` | Uses the punch-in's calendar date as the shift-start date and mishandles overnight shifts. | Punch in before the end of a 22:00–06:00 shift. | Calculate the start instant from the attendance date and employee shift. |
| 2 | `compute_late_minutes` | Grace rule is not implemented exactly: it compares floored minutes, so 09:40:59 may be treated as within grace. | Shift 09:30, punch at 09:40:59. | Compare elapsed seconds strictly to 600, then floor elapsed minutes. |
| 3 | `compute_work_hours` | Python `round` uses ties-to-even and floating-point arithmetic. | Test a duration exactly at a half-centesimal boundary. | Use `Decimal` and `ROUND_HALF_UP`. |
| 4 | `compute_overtime` | Uses the record date for the end time even for overnight shifts and has no 30-minute minimum. | 22:00–06:00 shift; punch out 06:20. | Build the correct shift-end instant on the next day and apply the 30-minute threshold. |
| 5 | `/health` | Returns success without pinging MongoDB. | Stop MongoDB and request `/health`. | Ping MongoDB and return 503 on failure. |
| 6 | `create_employee` | Check-then-insert is race-prone; no database uniqueness guarantee. | Send parallel creates with the same code. | Unique index; catch duplicate-key errors and return 409. |
| 7 | `create_employee` | Stores naive local `datetime.now()` and does not serialize `created_at` as epoch milliseconds. | Inspect response and BSON time. | Store aware UTC and serialize to epoch milliseconds. |
| 8 | `EmployeeIn` | Missing contract validation for employee code, email, date, shift format, lengths, and unequal shift times. | Submit malformed fields or equal shift times. | Add Pydantic constraints and validators. |
| 9 | `list_employees` | `skip=page * page_size` skips the first page. | Request page 1 and page 2. | Use `(page - 1) * page_size`. |
| 10 | `list_employees` | `total` counts all employees instead of the filtered department set. | Filter by a department. | Count with the same query filter. |
| 11 | `list_employees` | No stable sort, pagination bounds, or indexes. | Repeated requests with more than one page. | Sort by `emp_code`, validate page inputs, and index the query. |
| 12 | `punch_in` | Does not return 404 for unknown employees before accessing their shift. | Punch in using an unknown code. | Explicit employee existence check. |
| 13 | `punch_in` | Uses machine-local time and UTC date confusion instead of epoch milliseconds and IST calendar dates. | Punch near midnight IST or use `punched_at`. | Validate epoch milliseconds, normalize to UTC, derive IST attendance date. |
| 14 | `punch_in` | Status is not validated; derived fields and response include the wrong identifier shape. | Send `ABSENT` as punch-in or inspect response. | Accept presence statuses only and omit MongoDB `_id`/`id`. |
| 15 | `punch_in` | Duplicate check followed by insert can create a race. | Send concurrent same-day punch-ins. | Unique compound index on `(emp_code, date)` and catch duplicate-key errors. |
| 16 | `list_attendance` | Loads the entire matching result set into Python and sorts there. | Run against a large dataset. | Count/filter/sort/page in MongoDB with supporting indexes. |
| 17 | `list_attendance` | Sort order omits `emp_code` tie-breaker and response leaks `_id` while omitting epoch conversion/default fields. | Multiple employees share a date. | Sort by date descending then code ascending and normalize response documents. |
| 18 | All endpoints | No startup index creation or environment-priority handling beyond basic `.env` loading. | Fresh database or injected environment variables. | Create idempotent indexes at startup; real environment variables override `.env`. |
| 19 | Missing endpoints | Punch-out, correction, analytics and explain operations are absent. | Call any operation marked for implementation in the contract. | Implement all contract routes. |

**Reviewed and retained:** using `emp_code` as the public identifier is correct; MongoDB `_id` must remain internal. The two-collection model and string representation of calendar dates are intentional, not defects.

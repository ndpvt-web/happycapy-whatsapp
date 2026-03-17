# Babloo Fix Contract -- Aristotelian First Principles

## Axioms
- A1: Babloo code lives at /home/node/.claude/skills/happycapy-whatsapp/
- A2: Data dir is /home/node/.happycapy-whatsapp-2/
- A3: Only board = FBISE. Only classes = Grade 9 (SSC-I), Grade 10 (SSC-II)
- A4: WhatsApp uses *bold* (single), NOT **bold** (double/Markdown)
- A5: Each fix must have zero side effects on existing functionality
- A6: No other repos touched. Only Babloo.

## Work Items

### WI-1: Formatting Fix (whatsapp_channel.py ONLY)
- Add _convert_markdown_to_whatsapp(text) before send
- **bold** -> *bold*, ~~strike~~ -> ~strike~, ### headers -> *headers*
- Apply in send_text() after _strip_reasoning()

### WI-2: Exam Timetable + Board Constraint (proactive_engine.py ONLY)
- Populate exam_timetable with FBISE SSC-I (Grade 9) and SSC-II (Grade 10) 2026 data
- Constrain update_study_plan tool: board=FBISE only, class=Grade 9|Grade 10 only
- Fix countdown to use exam_timetable data

### WI-3: Auto-Enrollment + Reminders + Check-ins (main.py ONLY)
- On group message, auto-create student_plan from group name (Grade 9/10 detection)
- Default study_time=20:00, checkin 2h after (22:00), reminders_enabled=1
- Ensure heartbeat fires schedule_check properly
- Wire exam_date from exam_timetable into student plans

## FBISE SSC 2026 Exam Data

### SSC-I (Grade 9)
| Date | Day | Subject |
|------|-----|---------|
| 2026-04-01 | Wed | Math |
| 2026-04-04 | Sat | Tarjuma Quran |
| 2026-04-10 | Fri | Chemistry |
| 2026-04-13 | Mon | English |
| 2026-04-16 | Thu | Urdu |
| 2026-04-20 | Mon | Islamiat |
| 2026-04-24 | Fri | Physics |
| 2026-04-29 | Wed | Bio/Computer |

### SSC-II (Grade 10)
| Date | Day | Subject |
|------|-----|---------|
| 2026-03-31 | Tue | Physics |
| 2026-04-03 | Fri | Islamiat |
| 2026-04-06 | Mon | English |
| 2026-04-09 | Thu | Urdu |
| 2026-04-11 | Sat | Pak Studies |
| 2026-04-15 | Wed | Bio/Computer |
| 2026-04-18 | Sat | Bio/Comp (Practical) |
| 2026-04-21 | Tue | Chemistry |
| 2026-04-23 | Thu | Chemistry (Practical) |
| 2026-04-27 | Mon | Math |
| 2026-04-30 | Thu | Physics (Practical) |

## File Ownership (NO CONFLICTS)
- Agent 1 owns: src/whatsapp_channel.py
- Agent 2 owns: src/proactive_engine.py
- Agent 3 owns: src/main.py, src/context_builder.py

import sys
lines = []
with open('main.py', 'r') as f:
    lines = f.readlines()

# Find the index for the import of routine_intelligence_router
import_idx = -1
for i, line in enumerate(lines):
    if line.strip() == 'from app.api.routine_intelligence import router as routine_intelligence_router':
        import_idx = i
        break

if import_idx != -1:
    # Insert the import for notification_router after this line
    lines.insert(import_idx+1, 'from app.api.notification import router as notification_router\n')

# Find the index for the router inclusion of routine_intelligence_router
router_idx = -1
for i, line in enumerate(lines):
    if line.strip() == 'app.include_router(routine_intelligence_router, prefix=\"/api/v1\")':
        router_idx = i
        break

if router_idx != -1:
    # Insert the router inclusion for notification_router after this line
    lines.insert(router_idx+1, 'app.include_router(notification_router, prefix=\"/api/v1\")\n')

with open('main.py', 'w') as f:
    f.writelines(lines)
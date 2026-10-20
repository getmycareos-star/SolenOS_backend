import sys
lines = []
with open('main.py', 'r') as f:
    lines = f.readlines()

# We'll process the lines to remove duplicates and fix the broken line.

# First, let's find the index of the import for notification_router and keep only one.
# We'll also fix the broken line that has the \n in it.

new_lines = []
i = 0
while i < len(lines):
    line = lines[i]
    # Check if this line is the duplicate import for notification_router
    if line.strip() == 'from app.api.notification import router as notification_router':
        # We'll keep the first one and skip any subsequent ones until we see a different line.
        # But note: we might have two in a row.
        # We'll add this line and then skip any consecutive duplicates.
        new_lines.append(line)
        i += 1
        # Skip any consecutive duplicate lines
        while i < len(lines) and lines[i].strip() == 'from app.api.notification import router as notification_router':
            i += 1
        continue

    # Check if this line is the broken line that contains the \n
    if '\\n' in line:
        # Split the line by the \n character
        parts = line.split('\\n')
        # The first part should be the notification router inclusion
        # The second part should be the community router inclusion
        # We'll add two lines: the first part and then the second part
        # But note: the line might have other content? We assume it's exactly:
        #   app.include_router(notification_router, prefix="/api/v1")\napp.include_router(community_router, prefix="/api/v1")
        # We'll add the first part as a line and then the second part as a line.
        # We must ensure that each line ends with a newline.
        new_lines.append(parts[0] + '\n')
        new_lines.append(parts[1] + '\n')
        i += 1
        continue

    # Otherwise, just add the line
    new_lines.append(line)
    i += 1

# Now, we also need to remove duplicate router inclusions for notification_router in the inclusion section.
# We'll do a second pass for the router inclusions? Actually, we can do it in the same pass by checking for the inclusion line.
# But note: we already skipped the duplicate import lines. The router inclusion lines are separate.

# Let's do a second pass for the router inclusion lines.
# We'll process the new_lines to remove duplicate inclusion lines for notification_router.

final_lines = []
j = 0
while j < len(new_lines):
    line = new_lines[j]
    # Check if this line is the router inclusion for notification_router
    if line.strip() == 'app.include_router(notification_router, prefix=\"/api/v1\")':
        final_lines.append(line)
        j += 1
        # Skip any consecutive duplicates
        while j < len(new_lines) and new_lines[j].strip() == 'app.include_router(notification_router, prefix=\"/api/v1\")':
            j += 1
        continue
    final_lines.append(line)
    j += 1

# Write the file back
with open('main.py', 'w') as f:
    f.writelines(final_lines)
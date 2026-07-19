# VHS tape for the README demo GIF (https://github.com/charmbracelet/vhs)
#
# Prerequisites (run once before recording):
#   1. brew install vhs
#   2. Set the deny-list backend:   echo 'AUTH_BACKEND=server.auth_backends.static_policy.StaticPolicyBackend' >> deploy/server.env
#   3. Start the stack in another terminal:  ./start.sh   (leave it running, exit the shell it opens)
#   4. Record:  vhs docs/demo.tape   (produces docs/demo.gif)

Output docs/demo.gif

Set FontSize 16
Set Width 1100
Set Height 550
Set Theme "Dracula"
Set TypingSpeed 60ms

# Enter the jail
Type "docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.override.yml exec claude bash"
Enter
Sleep 2s

# A normal command works transparently
Type "git status"
Enter
Sleep 2.5s

# A destructive command is blocked by host-side policy
Type "terraform destroy"
Enter
Sleep 3s

# Deny list beats the git allow rules
Type "git push --force origin main"
Enter
Sleep 3s

# Anything unlisted fails closed
Type "aws iam create-access-key"
Enter
Sleep 4s

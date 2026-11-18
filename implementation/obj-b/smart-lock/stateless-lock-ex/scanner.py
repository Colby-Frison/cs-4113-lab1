
def scan(source):
    vulnerable = "sprintf(" in source
    finding = "Unsafe sprintf detected" if vulnerable else "No finding"
    return vulnerable, finding

print(scan("sprintf(buffer, input);"))

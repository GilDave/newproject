def parse_csv_line(line):
    fields = []
    i, n = 0, len(line)
    while True:
        if i < n and line[i] == '"':
            i += 1
            buf = []
            while True:
                if i >= n:
                    raise ValueError("unterminated quote")
                if line[i] == '"':
                    if i + 1 < n and line[i + 1] == '"':
                        buf.append('"')
                        i += 2
                        continue
                    i += 1
                    break
                buf.append(line[i])
                i += 1
            fields.append("".join(buf))
            if i < n and line[i] != ",":
                raise ValueError("text after closing quote")
        else:
            j = i
            while j < n and line[j] != ",":
                if line[j] == '"':
                    raise ValueError("quote inside an unquoted field")
                j += 1
            fields.append(line[i:j])
            i = j
        if i >= n:
            return fields
        i += 1  # skip the comma
        if i >= n:
            fields.append("")
            return fields

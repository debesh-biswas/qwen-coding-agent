"""Tool schemas sent to the model."""


def tool(name, desc, props, required):
    return {"type": "function", "function": {"name": name, "description": desc, "parameters": {
        "type": "object", "properties": props, "required": required}}}


STR = {"type": "string"}
TOOLS = [
    tool("list_files", "Recursively list files under a directory of the workspace.",
         {"path": dict(STR, description="Directory, default '.'")}, []),
    tool("read_file", "Read a text file.", {"path": STR}, ["path"]),
    tool("write_file", "Create or overwrite a file with exactly the given content. Creates parent directories.",
         {"path": STR, "content": STR}, ["path", "content"]),
    tool("delete_file", "Delete one file (not directories).", {"path": STR}, ["path"]),
    tool("csv_stats", "Row count, sum/average/min/max of a CSV column, optionally only rows where where_column equals where_value.",
         {"path": STR, "column": STR, "where_column": STR, "where_value": STR}, ["path", "column"]),
    tool("csv_row_sum", "Add the numeric values of several columns in the row(s) where id_column equals id_value.",
         {"path": STR, "id_column": STR, "id_value": STR, "columns": {"type": "array", "items": STR}},
         ["path", "id_column", "id_value", "columns"]),
    tool("csv_update", "Copy a CSV with column set to new_value in every row where id_column equals id_value; written to output (may equal path).",
         {"path": STR, "id_column": STR, "id_value": STR, "column": STR, "new_value": STR, "output": STR},
         ["path", "id_column", "id_value", "column", "new_value", "output"]),
    tool("csv_remove_rows", "Copy a CSV without the rows matching column <op> value; written to output (may equal path).",
         {"path": STR, "column": STR, "op": {"type": "string", "enum": [
             "equals", "not_equals", "contains", "greater_than", "less_than", "at_least", "at_most"]},
          "value": STR, "output": STR}, ["path", "column", "op", "value", "output"]),
    tool("copy_file", "Byte-exact copy of a file; creates parent directories.",
         {"source": STR, "dest": STR}, ["source", "dest"]),
    tool("calculate", "Evaluate an arithmetic expression (numbers, + - * / and parentheses only).",
         {"expression": STR}, ["expression"]),
    tool("finish", "End the task. message is the answer/result for the user.",
         {"status": {"type": "string", "enum": ["success", "error"]}, "message": STR}, ["status", "message"]),
]
INT = {"type": "integer"}
CODE_TOOLS = [
    tool("read_file", "Read a text file. For big files use start_line/max_lines (numbered lines) or search_file.",
         {"why": dict(STR, description="What the last result showed and what you want to find here"),
          "path": STR, "start_line": INT, "max_lines": INT}, ["why", "path"]),
    tool("run_command", "Run a shell command in the workspace (timeout default 60 s, max 120). Returns exit code, stdout, stderr.",
         {"command": STR, "expect": dict(STR, description="What a correct program must output here per the spec, worked out "
                                         "by hand BEFORE running (exact text for your own tiny test files; for the "
                                         "real data only the format, since example numbers in a spec come from a "
                                         "different file)"),
          "timeout": INT}, ["command", "expect"]),
    tool("edit_file", "Replace the single exact occurrence of old with new in a file.",
         {"path": STR, "old": STR, "new": STR}, ["path", "old", "new"]),
    tool("search_file", "Case-insensitive regex search in one file or every file under a directory; prints path:LINE: text. "
         "To see the lines around a match, use read_file with start_line.",
         {"why": dict(STR, description="What the last result showed and what you want to find with this search"),
          "path": STR, "pattern": STR, "max_matches": INT}, ["why", "path", "pattern"]),
    tool("note", "Save a short fact (ID, value, timestamp) you confirmed; notes stay visible even if the context is trimmed.",
         {"text": STR}, ["text"]),
]

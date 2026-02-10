# FM_CP — FileMaker Compose / Parse

Bidirectional converter between plain text script notation and FileMaker XML clipboard format.

## Why

FileMaker scripts live inside a proprietary IDE — you can't edit them as text files, and you can't paste plain text into the Script Workspace. This makes it difficult to collaborate on FileMaker scripts with AI tools like Claude, ChatGPT, or Copilot.

FM_CP bridges that gap:

1. **You write scripts as plain text** in any editor or AI chat
2. **FM_CP composes** the text into FileMaker's XML clipboard format
3. **Cmd+V** pastes working script steps into FileMaker

It works the other way too — copy steps from FileMaker, run `fm-cp -c`, and get readable plain text you can share, review, or feed back to an AI.

## How It Works

```
Plain Text  ──→  Parse  ──→  Validate  ──→  XML Generate  ──→  FM Clipboard
                                                                    ↓
                                                              Cmd+V into FM

FM Clipboard  ──→  Decompile  ──→  Plain Text  ──→  pbcopy (paste anywhere)
```

## Install

```bash
git clone https://github.com/nietsneuah/fm-cp.git
cd fm-cp
bash install.sh
```

That's it. The installer:
- Creates an isolated Python virtual environment (`~/.fm-cp/venv`)
- Installs all dependencies (including PyObjC for clipboard on macOS)
- Links `fm-cp` command to `/usr/local/bin`

No conda, no system Python conflicts.

### Uninstall

```bash
bash uninstall.sh
# or manually:
rm -rf ~/.fm-cp /usr/local/bin/fm-cp
```

## Usage

FM_CP auto-detects input format. One command, no subcommands needed.

```bash
# Clipboard workflows (macOS)
fm-cp -c                  # Auto-detect clipboard → compose or decompile
fm-cp dump                # Show raw FM XML from clipboard

# File workflows (any platform)
fm-cp script.txt              # Compose plain text → FM clipboard
fm-cp script.xml              # Decompile FM XML → readable text

# Output to file instead of clipboard
fm-cp script.txt -o out.xml   # Compose → save XML
fm-cp script.xml -o out.txt   # Decompile → save text
fm-cp dump -o raw.xml          # Dump clipboard → file
```

## Plain Text Format

```
# My Script Title
# -----------------------------------------------
Set Error Capture [ On ]
Allow User Abort [ Off ]

Set Variable [ $payload ; Value: Get(ScriptParameter) ]
Set Variable [ $custId ; Value: JSONGetElement ( $payload ; "customerId" ) ]

If [ IsEmpty ( $custId ) ]
    Show Custom Dialog [ "Error" ; "Missing customer ID" ; "OK" ]
    Exit Script [ Result: "error" ]
End If

Go to Layout [ "CustomerDetail" ]
Go to Record/Request/Page [ First ]
Perform Find
Set Field By Name [ "Customers::Status" ; "Active" ]
Commit Records

Loop
    Exit Loop If [ Get(FoundCount) = 0 ]
    Set Variable [ $name ; Value: Customers::Name ]
    Go to Record/Request/Page [ Next ]
End Loop

Go to Layout [ original layout ]
Exit Script [ Result: JSONSetElement ( "{}" ; "status" ; "ok" ; JSONString ) ]
```

## Supported Step Types

### Full Compose + Decompile (18 types)
Comment, Set Error Capture, Allow User Abort, Set Variable, Set Field By Name,
If / Else If / Else / End If, Loop / Exit Loop If / End Loop,
Show Custom Dialog, Exit Script, Commit Records, Perform Script,
Go to Layout, Go to Record, New Record, Enter Find Mode, Perform Find,
Sort Records, Insert from URL

### Decompile Only (8 types)
Set Field, Insert Text, New Window, Adjust Window, Refresh Window,
Halt Script, Configure LLM Template, LLM Request

Unrecognized steps display as: `StepName [id=N]`

## Notes

- **Internal IDs**: FileMaker uses internal IDs for layouts, scripts, fields, and tables.
  Composed XML uses names only — FM may show `<unknown>` for unresolved references.
  `Set Field By Name` avoids this since it uses calculation strings, not object references.

- **Disabled steps**: Decompile shows disabled steps with `//` prefix.

- **Clipboard format**: FM uses custom pasteboard type `XMSS` (script steps) and `XMSC` (single step).

## Requirements

- Python 3.9+
- macOS for clipboard features (PyObjC)
- File I/O works on any platform

## License

MIT

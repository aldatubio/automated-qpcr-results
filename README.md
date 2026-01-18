# PANDAA Automated Analysis Software

This repository contains two companion tools: ReFocus Assistant, for use with HIVDR kits and experiments, and EpiFocus Assistant, for use with viral hemorrhagic fever (VHF) kits and experiments.

**What these tools do:** after the user provides a raw results file (exported from qPCR software), the Assistant tool will automatically parse results, providing qualitative outcomes by qPCR well. Results are provided as a CSV file and, optionally, as a traceability-friendly PDF.

**Who should use it (and when):** Currently, these tools are meant for internal R&D use only here at Aldatu; with additional development (e.g. a purchased code signing certificate), this tool is ultimately meant for customer use, or perhaps internal manufacturing use.

**How to run it:** After downloading and running the installer executable for ReFocus Assistant or EpiFocus Assistant, double-click the application to run the tool. Choose the PANDAA assay and qPCR machine used, then upload raw qPCR results when prompted. A dialog box will appear with the location of the CSV file and/or PDF file with parsed results.

**Where to edit parameters:** For non-developer parameterization, there are several TOML files available. Parameters can be edited in any text editor.
- A `config.toml` file is available in each Assistant tool's sub-folder (`/vhf/` or `/hiv/`). Here, tool-specific options can be configured, such as display name, qPCR machines included in the selection menu, and Cq cutoffs used.
- The `/shared/` folder contains the `assays.toml` file. Here, PANDAA assays can be defined. Because this is a shared file, these assays can be used in either tool (whether they are visible in selection menus can be configured in the `config.toml` file).

TOML files are runtime-editable and can be used to configure existing executables.

**What to read next:** For more information, see the [full guide](https://github.com/aldatubio/automated-qpcr-results/blob/main/docs/Software_User_Guide.pdf).

## Contributions
**Note:** All contributions prior to July 2025 were authored by Lucy Langenberg.


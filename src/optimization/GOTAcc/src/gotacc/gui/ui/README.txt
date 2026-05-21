Generated UI skeletons for GOTAcc Studio

Files:
- main_window.ui           Integrated main window with stacked pages and status dock
- page_task_builder.ui    Standalone Task Builder page
- page_machine.ui         Standalone Machine Interface page
- page_run_monitor.ui     Standalone Run Monitor page

Recommended repo path:
- src/gotacc/gui/ui/

Suggested sibling packages:
- src/gotacc/gui/views/
- src/gotacc/gui/controllers/
- src/gotacc/gui/services/
- src/gotacc/gui/widgets/

Compile example:
pyuic5 src/gotacc/gui/ui/main_window.ui -o src/gotacc/gui/views/ui_main_window.py

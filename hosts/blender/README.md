# RoboVision Blender host

Install `robovision_blender` as a Blender add-on, enable it, then start the bridge from **3D View → Sidebar → RoboVision**. Default transport is `127.0.0.1:9877`.

The add-on deliberately does not use a threaded `bpy` execution model. Its listener is non-blocking and polled by Blender's timer/event loop, so all host commands run on Blender's editor thread.

Initial Gate-1 surface:

- system hello/ping/capabilities
- scene describe/snapshot/diff/raycast
- stable object IDs and duplicate repair
- object inspect/create/delete/duplicate/transform/parent
- mesh inspect/validate/bevel/extrude with mesh-revision preconditions
- modifier list/add/set/apply
- viewport inspect/focus/axis/capture
- transaction begin/commit/rollback with fingerprint verification

The add-on does not expose arbitrary Python execution by default.

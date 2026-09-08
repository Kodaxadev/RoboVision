# RoboVision Unity host

Install from Git using Unity Package Manager with:

```text
https://github.com/Kodaxadev/RoboVision.git?path=/hosts/unity/Packages/com.kodaxa.robovision
```

The Unity host follows the same RoboVision 1.0 contract as Blender. It uses Unity `GlobalObjectId` for authoring-time identity, editor update dispatch, Undo groups, `SerializedObject`/`SerializedProperty` where appropriate, and SceneView/GameView evidence capture.

Default port is `127.0.0.1:9878` so Blender and Unity can be open simultaneously.

Gate 4 is not considered passed until this package has been compiled and exercised inside the target Unity 6 editor.

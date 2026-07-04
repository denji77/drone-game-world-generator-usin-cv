// Phase 8: headless scene assembly (and optional player build). Lives under an "Editor"
// folder. Invoked by run.py when unity.run_build: true, or manually:
//   Unity -batchmode -quit -projectPath <path> -executeMethod BuildRunner.Build
//         [-buildPlayer true] [-buildTarget StandaloneWindows64]
// Requires: Assets/Generated/{ground.png, tracks_world.json, scene_meta.json} (deployed by
// the pipeline) and Assets/Prefabs/{Person, Vehicle}.prefab (made once, see README).
using System.IO;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

public static class BuildRunner
{
    const string GEN = "Assets/Generated";

    [System.Serializable]
    class Meta { public float x0, z0, W_m, H_m, ppm; public string scale_status; }

    public static void Build()
    {
        bool buildPlayer = HasFlag("-buildPlayer", "true");
        var meta = JsonUtility.FromJson<Meta>(File.ReadAllText($"{GEN}/scene_meta.json"));

        var scene = EditorSceneManager.NewScene(NewSceneSetup.DefaultGameObjects, NewSceneMode.Single);

        // ground plane (Unity Plane = 10x10 units) over the auto-computed world bounds
        AssetDatabase.ImportAsset($"{GEN}/ground.png");
        var ground = GameObject.CreatePrimitive(PrimitiveType.Plane);
        ground.name = "Ground";
        ground.transform.localScale = new Vector3(meta.W_m / 10f, 1f, meta.H_m / 10f);
        ground.transform.position = new Vector3(meta.x0 + meta.W_m / 2f, 0f, meta.z0 + meta.H_m / 2f);
        ground.transform.rotation = Quaternion.identity; // plane +X/+Z = world +X/+Z, explicitly
        var mat = new Material(Shader.Find("Universal Render Pipeline/Lit"));
        mat.SetTexture("_BaseMap", AssetDatabase.LoadAssetAtPath<Texture2D>($"{GEN}/ground.png"));
        ground.GetComponent<MeshRenderer>().sharedMaterial = mat;

        // playback driver
        var driver = new GameObject("AgentPlayback").AddComponent<AgentPlayback>();
        driver.tracksJson = AssetDatabase.LoadAssetAtPath<TextAsset>($"{GEN}/tracks_world.json");
        driver.personPrefab = AssetDatabase.LoadAssetAtPath<GameObject>("Assets/Prefabs/Person.prefab");
        driver.vehiclePrefab = AssetDatabase.LoadAssetAtPath<GameObject>("Assets/Prefabs/Vehicle.prefab");
        if (driver.personPrefab == null || driver.vehiclePrefab == null)
            Debug.LogWarning("Person/Vehicle prefab missing at Assets/Prefabs/ - agents won't spawn.");

        new GameObject("DetectionLogger").AddComponent<DetectionLogger>();

        // top-down camera over the world center
        var cam = new GameObject("TopCam").AddComponent<Camera>();
        cam.transform.position = new Vector3(meta.x0 + meta.W_m / 2f,
                                             Mathf.Max(meta.W_m, meta.H_m),
                                             meta.z0 + meta.H_m / 2f);
        cam.transform.eulerAngles = new Vector3(90, 0, 0);

        EditorSceneManager.SaveScene(scene, $"{GEN}/Recreation.unity");
        Debug.Log($"BuildRunner: saved {GEN}/Recreation.unity");

        if (buildPlayer)
        {
            var target = ParseTarget();
            string outPath = target == BuildTarget.WebGL ? "Build/web" : "Build/app.exe";
            BuildPipeline.BuildPlayer(new[] { $"{GEN}/Recreation.unity" }, outPath, target,
                                      BuildOptions.None);
        }
    }

    static bool HasFlag(string flag, string val)
    {
        var a = System.Environment.GetCommandLineArgs();
        for (int i = 0; i < a.Length - 1; i++)
            if (a[i] == flag && a[i + 1] == val) return true;
        return false;
    }

    static BuildTarget ParseTarget()
    {
        var a = System.Environment.GetCommandLineArgs();
        for (int i = 0; i < a.Length - 1; i++)
            if (a[i] == "-buildTarget" && System.Enum.TryParse(a[i + 1], out BuildTarget t)) return t;
        return BuildTarget.StandaloneWindows64;
    }
}

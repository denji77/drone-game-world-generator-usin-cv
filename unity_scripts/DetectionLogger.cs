// Phase 7: CSV log of who-was-in-which-zone-when. One per scene (empty GameObject).
// Timestamps are VIDEO-seconds (AgentPlayback.SimTime), passed in by the caller, so the
// CSV lines up with the footage at any timeScale.
using System.IO;
using UnityEngine;

public class DetectionLogger : MonoBehaviour
{
    public static DetectionLogger I;
    StreamWriter w;

    void Awake()
    {
        I = this;
        string path = Path.Combine(Application.persistentDataPath, "detections.csv");
        w = new StreamWriter(path) { AutoFlush = true };
        w.WriteLine("sim_time,object_type,zone_type,zone_id,x,y,z");
        Debug.Log($"DetectionLogger writing to: {path}");
    }

    public void Log(float simTime, string type, ZoneType zt, string zid, Vector3 p) =>
        w.WriteLine($"{simTime:F2},{type},{zt},{zid},{p.x:F2},{p.y:F2},{p.z:F2}");

    void OnApplicationQuit() => w?.Dispose();
}

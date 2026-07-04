// Phase 6: replay the pipeline's world-space tracks (Assets/Generated/tracks_world.json)
// as moving prefabs. Attach to an empty GameObject; assign tracksJson + the two prefabs.
using System.Collections.Generic;
using UnityEngine;

[System.Serializable] public class Pt    { public float t, x, z; }
[System.Serializable] public class Track { public int id; public string @class; public List<Pt> pts; }
[System.Serializable] public class Data  { public float video_fps; public List<Track> tracks; }

public class AgentPlayback : MonoBehaviour
{
    public TextAsset tracksJson;                 // Assets/Generated/tracks_world.json
    public GameObject personPrefab, vehiclePrefab;
    public float groundY = 0f, timeScale = 1f;
    public bool loop = true;

    // Video-seconds - the ONE clock. The zone logger reads this, NOT Time.time:
    // wall-clock stamps desync from the footage the moment timeScale != 1.
    public static float SimTime { get; private set; }

    readonly List<(Track tr, Transform go)> agents = new();
    float duration;

    void Start()
    {
        var data = JsonUtility.FromJson<Data>(tracksJson.text);
        foreach (var tr in data.tracks)
        {
            if (tr.pts == null || tr.pts.Count == 0) continue;
            var go = Instantiate(tr.@class == "person" ? personPrefab : vehiclePrefab).transform;
            go.name = $"{tr.@class}_{tr.id}";
            go.gameObject.SetActive(false);
            agents.Add((tr, go));
            duration = Mathf.Max(duration, tr.pts[tr.pts.Count - 1].t);
        }
        SimTime = 0f;
    }

    void Update()
    {
        SimTime += Time.deltaTime * timeScale;
        if (loop && duration > 0f && SimTime > duration) SimTime = 0f;
        float clock = SimTime;

        foreach (var (tr, go) in agents)
        {
            var p = tr.pts;
            if (clock < p[0].t || clock > p[p.Count - 1].t)
            {
                go.gameObject.SetActive(false);
                continue;
            }
            go.gameObject.SetActive(true);
            int i = 0;
            while (i < p.Count - 1 && p[i + 1].t < clock) i++;
            var a = p[i];
            var b = p[Mathf.Min(i + 1, p.Count - 1)];
            float u = Mathf.Approximately(b.t, a.t) ? 0f : (clock - a.t) / (b.t - a.t);
            Vector3 pos = new Vector3(Mathf.Lerp(a.x, b.x, u), groundY, Mathf.Lerp(a.z, b.z, u));
            Vector3 dir = pos - go.position;
            if (dir.sqrMagnitude > 1e-5f) go.rotation = Quaternion.LookRotation(dir.normalized);
            go.position = pos;
        }
    }
}

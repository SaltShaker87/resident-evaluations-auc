//! What to suggest on the Choices screen.
//!
//! All of it is decided from `SystemInfo` and nothing else, so it can be
//! tested for every kind of machine without owning one. The tiers are
//! CONTRACT.md's table; the wording is what the user actually reads, so it is
//! spelled out here rather than assembled in the React half.

use crate::engine::types::{
    AiRecommendation, GpuVendor, ModelChoice, NemotronMode, NemotronRecommendation, Recommendation,
    SystemInfo,
};

pub const MODEL_SMALL: &str = "qwen3.5:4b";
pub const MODEL_MEDIUM: &str = "qwen3.5:9b";
pub const MODEL_NEMOTRON: &str = "nemotron-3.5-lightning";

/// Always pulled alongside the chosen model when AI is on: it is what builds
/// and searches the ACGME index, and it is not a choice.
pub const EMBED_MODEL: &str = "qwen3-embedding:0.6b";

/// Nemotron's two containers together need about this much GPU memory.
const NEMOTRON_MIN_GB: f64 = 16.0;

/// Over this much GPU memory and Nemotron 3.5 Lightning is the recommendation.
const NEMOTRON_MODEL_MIN_GB: f64 = 32.0;

struct ModelSpec {
    name: &'static str,
    label: &'static str,
    description: &'static str,
    min_memory_gb: u32,
}

/// Smallest to largest, which is also the order the screen shows them in.
const MODELS: [ModelSpec; 3] = [
    ModelSpec {
        name: MODEL_SMALL,
        label: "Qwen 3.5 4B",
        description: "The smallest model that writes usable summaries; fine on a laptop GPU.",
        min_memory_gb: 8,
    },
    ModelSpec {
        name: MODEL_MEDIUM,
        label: "Qwen 3.5 9B",
        description: "Noticeably better wording than the 4B, and still quick on a desktop card.",
        min_memory_gb: 16,
    },
    ModelSpec {
        name: MODEL_NEMOTRON,
        label: "NVIDIA Nemotron 3.5 Lightning",
        description: "The best summaries AUC can write, and what it was tuned on.",
        min_memory_gb: 33,
    },
];

pub fn recommend(system: &SystemInfo) -> Recommendation {
    let memory = system.gpu.memory_gb;
    let choice = recommended_model(memory);

    let models = MODELS
        .iter()
        .map(|spec| ModelChoice {
            name: spec.name.to_string(),
            label: spec.label.to_string(),
            description: spec.description.to_string(),
            min_memory_gb: spec.min_memory_gb,
            recommended: choice == Some(spec.name),
            fits: memory.is_some_and(|gb| gb >= spec.min_memory_gb as f64),
        })
        .collect();

    Recommendation {
        ai: ai_recommendation(system, choice),
        models,
        best_message: best_message(memory, choice),
        nemotron: nemotron_recommendation(system),
    }
}

/// The tiers from CONTRACT.md, and the only place they are decided.
fn recommended_model(memory_gb: Option<f64>) -> Option<&'static str> {
    let gb = memory_gb?;
    if gb < 8.0 {
        None
    } else if gb < 16.0 {
        Some(MODEL_SMALL)
    } else if gb <= NEMOTRON_MODEL_MIN_GB {
        Some(MODEL_MEDIUM)
    } else {
        Some(MODEL_NEMOTRON)
    }
}

fn ai_recommendation(system: &SystemInfo, choice: Option<&'static str>) -> AiRecommendation {
    let label = choice.and_then(label_for);
    match (choice, label, system.gpu.memory_gb) {
        (Some(_), Some(label), Some(gb)) if system.gpu.memory_unified => AiRecommendation {
            recommended: true,
            reason: format!(
                "This machine has {} GB of memory shared between its processor and graphics, \
                 which is enough for {label}.",
                format_gb(gb)
            ),
        },
        (Some(_), Some(label), Some(gb)) => AiRecommendation {
            recommended: true,
            reason: format!(
                "This machine has {} GB of GPU memory, which is enough for {label}.",
                format_gb(gb)
            ),
        },
        _ => AiRecommendation {
            recommended: false,
            reason: no_ai_reason(system),
        },
    }
}

/// Why the AI features are being suggested off. Each of these is something
/// the user can act on, or at least understand, without knowing what a GPU is.
fn no_ai_reason(system: &SystemInfo) -> String {
    match (system.gpu.vendor, system.gpu.memory_gb) {
        (GpuVendor::None, _) => "No graphics card was found, and the AI summaries need one. \
             Everything else in AUC works: residents, notes, follow-ups, the CCC drawer and PDF export."
            .to_string(),
        (GpuVendor::Nvidia, None) => "AUC could not read how much memory this graphics card has, \
             so it cannot promise the AI summaries will run here. You can still turn them on."
            .to_string(),
        (GpuVendor::Nvidia, Some(gb)) => format!(
            "This graphics card has {} GB of memory and the smallest model needs 8 GB. \
             Everything else in AUC works.",
            format_gb(gb)
        ),
        (vendor, _) => format!(
            "The AI summaries need an NVIDIA graphics card and this machine has {}. \
             Everything else in AUC works.",
            match vendor {
                GpuVendor::Amd => "an AMD one",
                GpuVendor::Apple => "Apple's own",
                _ => "another kind",
            }
        ),
    }
}

/// Always starts with the same sentence, because it is a statement about AUC
/// rather than about this machine; what follows explains what this machine can
/// do about it.
fn best_message(memory_gb: Option<f64>, choice: Option<&'static str>) -> String {
    const OPENING: &str = "AUC runs best on NVIDIA Nemotron 3.5 Lightning.";
    if choice == Some(MODEL_NEMOTRON) {
        return format!("{OPENING} This machine can run it.");
    }
    match memory_gb {
        Some(gb) => format!(
            "{OPENING} It needs more than 32 GB of GPU memory; this machine has {} GB.",
            format_gb(gb)
        ),
        None => format!(
            "{OPENING} It needs more than 32 GB of GPU memory; \
             this machine has no graphics card memory to report."
        ),
    }
}

fn nemotron_recommendation(system: &SystemInfo) -> NemotronRecommendation {
    // The containers are Linux-only, and so is the whole of version 1.
    if system.os != "linux" {
        return NemotronRecommendation {
            mode: NemotronMode::Unavailable,
            reason: "The NVIDIA Nemotron engine runs only on Linux.".to_string(),
        };
    }
    if system.gpu.is_gb10 {
        return NemotronRecommendation {
            mode: NemotronMode::Default,
            reason:
                "This machine has NVIDIA's GB10 chip, which the Nemotron engine was built for, \
                 so it is the default here."
                    .to_string(),
        };
    }
    match (system.gpu.vendor, system.gpu.memory_gb) {
        (GpuVendor::Nvidia, Some(gb)) if gb >= NEMOTRON_MIN_GB => NemotronRecommendation {
            mode: NemotronMode::Optional,
            reason: format!(
                "This NVIDIA graphics card has {} GB of memory, enough for the Nemotron containers. \
                 It is optional here: the Standard engine is the default, and Nemotron can be added later.",
                format_gb(gb)
            ),
        },
        (GpuVendor::Nvidia, Some(gb)) => NemotronRecommendation {
            mode: NemotronMode::Unavailable,
            reason: format!(
                "The Nemotron containers need about 16 GB of graphics memory; this machine has {} GB.",
                format_gb(gb)
            ),
        },
        (GpuVendor::Nvidia, None) => NemotronRecommendation {
            mode: NemotronMode::Unavailable,
            reason: "AUC could not read how much memory this NVIDIA graphics card has, \
                 and the Nemotron containers need about 16 GB."
                .to_string(),
        },
        _ => NemotronRecommendation {
            mode: NemotronMode::Unavailable,
            reason: "The Nemotron engine needs an NVIDIA graphics card; this machine has none."
                .to_string(),
        },
    }
}

fn label_for(name: &str) -> Option<&'static str> {
    MODELS
        .iter()
        .find(|spec| spec.name == name)
        .map(|spec| spec.label)
}

/// 24 rather than 24.0, but 125.8 rather than 126 — the number people would
/// read off the box.
pub fn format_gb(gb: f64) -> String {
    if (gb - gb.round()).abs() < 0.05 {
        format!("{}", gb.round() as i64)
    } else {
        format!("{gb:.1}")
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::engine::types::GpuInfo;

    fn machine(vendor: GpuVendor, memory_gb: Option<f64>, is_gb10: bool) -> SystemInfo {
        let mut system = SystemInfo::blank();
        system.os = "linux".to_string();
        system.supported = true;
        system.gpu = GpuInfo {
            vendor,
            name: Some("test GPU".to_string()),
            memory_gb,
            is_gb10,
            memory_unified: false,
        };
        system
    }

    fn nvidia(memory_gb: f64) -> SystemInfo {
        machine(GpuVendor::Nvidia, Some(memory_gb), false)
    }

    fn chosen(system: &SystemInfo) -> Option<String> {
        recommend(system).default_model().map(str::to_string)
    }

    // ---- the four tiers, at their edges ----

    #[test]
    fn a_machine_with_no_gpu_is_offered_no_model() {
        let rec = recommend(&machine(GpuVendor::None, None, false));
        assert!(!rec.ai.recommended);
        assert!(rec.models.iter().all(|m| !m.recommended));
        assert_eq!(rec.models.len(), 3);
        assert!(rec.models.iter().all(|m| !m.fits));
    }

    #[test]
    fn under_eight_gigabytes_is_no_model() {
        assert_eq!(chosen(&nvidia(7.0)), None);
        assert!(!recommend(&nvidia(7.0)).ai.recommended);
    }

    #[test]
    fn eight_gigabytes_exactly_gets_the_small_model() {
        assert_eq!(chosen(&nvidia(8.0)).as_deref(), Some(MODEL_SMALL));
        assert!(recommend(&nvidia(8.0)).ai.recommended);
    }

    #[test]
    fn fifteen_gigabytes_still_gets_the_small_model() {
        assert_eq!(chosen(&nvidia(15.0)).as_deref(), Some(MODEL_SMALL));
    }

    #[test]
    fn sixteen_gigabytes_moves_up_to_the_medium_model() {
        assert_eq!(chosen(&nvidia(16.0)).as_deref(), Some(MODEL_MEDIUM));
    }

    #[test]
    fn thirty_two_gigabytes_is_still_the_medium_model() {
        assert_eq!(chosen(&nvidia(32.0)).as_deref(), Some(MODEL_MEDIUM));
    }

    #[test]
    fn thirty_three_gigabytes_gets_nemotron() {
        assert_eq!(chosen(&nvidia(33.0)).as_deref(), Some(MODEL_NEMOTRON));
    }

    #[test]
    fn a_spark_gets_nemotron() {
        let spark = machine(GpuVendor::Nvidia, Some(120.0), true);
        assert_eq!(chosen(&spark).as_deref(), Some(MODEL_NEMOTRON));
    }

    #[test]
    fn a_spark_whose_memory_is_shared_says_so_and_still_gets_nemotron() {
        let mut spark = machine(GpuVendor::Nvidia, Some(128.0), true);
        spark.gpu.memory_unified = true;
        let rec = recommend(&spark);
        assert_eq!(rec.default_model(), Some(MODEL_NEMOTRON));
        assert!(rec.ai.recommended);
        assert!(
            rec.ai.reason.contains("128 GB of memory shared"),
            "{}",
            rec.ai.reason
        );
        assert!(rec.models.iter().all(|m| m.fits), "128 GB fits every model");
        assert_eq!(rec.nemotron.mode, NemotronMode::Default);
    }

    #[test]
    fn unknown_gpu_memory_recommends_nothing_but_does_not_pretend_it_is_zero() {
        let rec = recommend(&machine(GpuVendor::Nvidia, None, false));
        assert!(!rec.ai.recommended);
        assert!(rec.models.iter().all(|m| !m.recommended && !m.fits));
        assert!(
            rec.ai.reason.contains("could not read"),
            "unhelpful reason: {}",
            rec.ai.reason
        );
    }

    #[test]
    fn exactly_one_model_is_recommended_when_ai_is_recommended() {
        for gb in [8.0, 15.0, 16.0, 32.0, 33.0, 120.0] {
            let rec = recommend(&nvidia(gb));
            assert!(rec.ai.recommended, "{gb} GB should support AI");
            assert_eq!(
                rec.models.iter().filter(|m| m.recommended).count(),
                1,
                "{gb} GB should recommend exactly one model"
            );
        }
    }

    #[test]
    fn models_are_always_listed_smallest_to_largest_with_their_contract_names() {
        let rec = recommend(&nvidia(24.0));
        let names: Vec<&str> = rec.models.iter().map(|m| m.name.as_str()).collect();
        assert_eq!(names, vec![MODEL_SMALL, MODEL_MEDIUM, MODEL_NEMOTRON]);
        let labels: Vec<&str> = rec.models.iter().map(|m| m.label.as_str()).collect();
        assert_eq!(
            labels,
            vec![
                "Qwen 3.5 4B",
                "Qwen 3.5 9B",
                "NVIDIA Nemotron 3.5 Lightning"
            ]
        );
        let minimums: Vec<u32> = rec.models.iter().map(|m| m.min_memory_gb).collect();
        assert_eq!(minimums, vec![8, 16, 33]);
    }

    #[test]
    fn fits_is_about_this_machine_not_about_the_recommendation() {
        let rec = recommend(&nvidia(16.0));
        assert!(rec.models[0].fits);
        assert!(rec.models[1].fits);
        assert!(!rec.models[2].fits, "33 GB is more than 16 GB");
    }

    // ---- best_message ----

    #[test]
    fn best_message_always_opens_with_the_same_sentence() {
        for system in [
            machine(GpuVendor::None, None, false),
            nvidia(8.0),
            nvidia(24.0),
            nvidia(120.0),
        ] {
            assert!(
                recommend(&system)
                    .best_message
                    .starts_with("AUC runs best on NVIDIA Nemotron 3.5 Lightning."),
                "wrong opening: {}",
                recommend(&system).best_message
            );
        }
    }

    #[test]
    fn best_message_says_what_is_missing_when_nemotron_is_not_the_recommendation() {
        assert_eq!(
            recommend(&nvidia(24.0)).best_message,
            "AUC runs best on NVIDIA Nemotron 3.5 Lightning. \
             It needs more than 32 GB of GPU memory; this machine has 24 GB."
        );
    }

    #[test]
    fn best_message_is_short_and_positive_when_nemotron_is_the_recommendation() {
        assert_eq!(
            recommend(&nvidia(48.0)).best_message,
            "AUC runs best on NVIDIA Nemotron 3.5 Lightning. This machine can run it."
        );
    }

    #[test]
    fn best_message_does_not_claim_zero_gigabytes_when_it_does_not_know() {
        let message = recommend(&machine(GpuVendor::None, None, false)).best_message;
        assert!(
            message.contains("no graphics card memory to report"),
            "{message}"
        );
        assert!(!message.contains("0 GB"), "{message}");
    }

    // ---- nemotron mode ----

    #[test]
    fn nemotron_is_the_default_on_a_gb10() {
        let rec = recommend(&machine(GpuVendor::Nvidia, Some(120.0), true));
        assert_eq!(rec.nemotron.mode, NemotronMode::Default);
        assert!(rec.nemotron.reason.contains("GB10"));
    }

    #[test]
    fn nemotron_is_optional_on_a_big_enough_nvidia_card() {
        for gb in [16.0, 24.0, 48.0] {
            let rec = recommend(&nvidia(gb));
            assert_eq!(rec.nemotron.mode, NemotronMode::Optional, "{gb} GB");
        }
    }

    #[test]
    fn nemotron_is_unavailable_below_sixteen_gigabytes() {
        let rec = recommend(&nvidia(15.0));
        assert_eq!(rec.nemotron.mode, NemotronMode::Unavailable);
        assert!(
            rec.nemotron.reason.contains("15 GB"),
            "{}",
            rec.nemotron.reason
        );
    }

    #[test]
    fn nemotron_is_unavailable_without_an_nvidia_card() {
        for vendor in [GpuVendor::None, GpuVendor::Amd, GpuVendor::Apple] {
            let rec = recommend(&machine(vendor, Some(64.0), false));
            assert_eq!(rec.nemotron.mode, NemotronMode::Unavailable, "{vendor:?}");
            assert!(rec.nemotron.reason.contains("NVIDIA"));
        }
    }

    #[test]
    fn nemotron_is_unavailable_when_the_gpu_memory_is_unknown() {
        let rec = recommend(&machine(GpuVendor::Nvidia, None, false));
        assert_eq!(rec.nemotron.mode, NemotronMode::Unavailable);
    }

    #[test]
    fn nemotron_is_linux_only() {
        let mut mac = machine(GpuVendor::Apple, Some(64.0), false);
        mac.os = "macos".to_string();
        let rec = recommend(&mac);
        assert_eq!(rec.nemotron.mode, NemotronMode::Unavailable);
        assert!(rec.nemotron.reason.contains("only on Linux"));
    }

    #[test]
    fn gigabytes_are_written_the_way_people_say_them() {
        assert_eq!(format_gb(24.0), "24");
        assert_eq!(format_gb(120.0), "120");
        assert_eq!(format_gb(125.8), "125.8");
    }
}

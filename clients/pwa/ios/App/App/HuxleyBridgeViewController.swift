import Capacitor
import UIKit

/// Registers the app's own plugins (HuxleyAudioPlugin) with the Capacitor
/// bridge. SceneDelegate (and Main.storyboard) use it instead of
/// CAPBridgeViewController.
class HuxleyBridgeViewController: CAPBridgeViewController {
    override open func capacitorDidLoad() {
        bridge?.registerPluginInstance(HuxleyAudioPlugin())
    }
}

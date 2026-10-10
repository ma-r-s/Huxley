import Capacitor
import UIKit

/// Registers the app's own plugins (HuxleyAudioPlugin) with the Capacitor
/// bridge. SceneDelegate builds the window with it; there is no storyboard
/// (Capacitor's template had one too, which instantiated a second, unused
/// bridge controller).
class HuxleyBridgeViewController: CAPBridgeViewController {
    override open func capacitorDidLoad() {
        bridge?.registerPluginInstance(HuxleyAudioPlugin())
    }
}
